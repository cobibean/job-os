// @vitest-environment node

import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import type { BrowserRestoreState, BrowserState } from '../shared/contracts.js'
import {
  BROWSER_RESTORE_CANDIDATE_LIMIT,
  registerBrowserRestoreHandler
} from './browserIpc.js'

test('actual IPC restore handler repairs candidates before the fifty-tab cap', async () => {
  const handlers = new Map<string, (event: IpcMainInvokeEvent, state: unknown) => Promise<BrowserState>>()
  const ipc = {
    handle: vi.fn((channel: string, handler: (event: IpcMainInvokeEvent, state: unknown) => Promise<BrowserState>) => {
      handlers.set(channel, handler)
    })
  } as unknown as Pick<IpcMain, 'handle'>
  const restore = vi.fn(async (state: BrowserRestoreState): Promise<BrowserState> => ({
    ...state,
    tabs: state.tabs.map(tab => ({
      ...tab,
      loading: false,
      canGoBack: false,
      canGoForward: false,
      crashed: false,
      error: null,
      blockedUrl: null
    })),
    download: null,
    notice: null
  }))
  registerBrowserRestoreHandler(ipc, () => ({ restore }))
  const validTabs = Array.from({ length: 50 }, (_, index) => ({
    tabId: `tab-${index}`,
    url: `https://example.com/${index}`,
    title: `Tab ${index}`,
    faviconUrl: null,
    associatedJobId: null
  }))
  const rawState = {
    tabs: [
      { tabId: 'invalid', url: 'https://-foo.example/', title: 'Invalid', faviconUrl: null, associatedJobId: null },
      validTabs[0],
      { ...validTabs[0], url: 'https://duplicate.example/' },
      { tabId: 'malformed', url: 'https://[::1', title: 'Malformed', faviconUrl: null, associatedJobId: null },
      ...validTabs.slice(1)
    ],
    activeTabId: 'tab-49'
  }

  const handler = handlers.get('jobos:browser:restore')
  if (!handler) throw new Error('Browser restore IPC handler was not registered')
  await handler({} as IpcMainInvokeEvent, rawState)

  expect(rawState.tabs.length).toBeGreaterThan(50)
  expect(restore).toHaveBeenCalledTimes(1)
  expect(restore.mock.calls[0]?.[0].tabs.map(tab => tab.tabId)).toEqual(
    Array.from({ length: 50 }, (_, index) => `tab-${index}`)
  )
  expect(restore.mock.calls[0]?.[0].activeTabId).toBe('tab-49')
})

test('IPC restore handler keeps the raw candidate stream bounded', async () => {
  const handlers = new Map<string, (event: IpcMainInvokeEvent, state: unknown) => Promise<BrowserState>>()
  const ipc = {
    handle: vi.fn((channel: string, handler: (event: IpcMainInvokeEvent, state: unknown) => Promise<BrowserState>) => {
      handlers.set(channel, handler)
    })
  } as unknown as Pick<IpcMain, 'handle'>
  const restore = vi.fn()
  registerBrowserRestoreHandler(ipc, () => ({ restore }))
  const handler = handlers.get('jobos:browser:restore')
  if (!handler) throw new Error('Browser restore IPC handler was not registered')

  await expect(handler({} as IpcMainInvokeEvent, {
    tabs: Array.from({ length: BROWSER_RESTORE_CANDIDATE_LIMIT + 1 }, () => null),
    activeTabId: null
  })).rejects.toThrow('Invalid browser restore state')
  expect(restore).not.toHaveBeenCalled()
})
