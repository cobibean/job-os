import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerConnectivityIpc } from './connectivityIpc.js'
import { registerDiagnosticsIpc } from './diagnosticsIpc.js'
import { registerSetupIpc } from './setupIpc.js'
import { registerShellIpc } from './shellIpc.js'

function ipcHarness() {
  const handlers = new Map<string, (...arguments_: never[]) => unknown>()
  return {
    handlers,
    ipc: { handle: (channel: string, handler: (...arguments_: never[]) => unknown) => handlers.set(channel, handler) } as unknown as Pick<IpcMain, 'handle'>
  }
}

test('shell validates before opening external URLs', async () => {
  const { ipc, handlers } = ipcHarness()
  const open = vi.fn(async () => undefined)
  registerShellIpc(ipc, vi.fn(), open)
  const event = {} as IpcMainInvokeEvent
  await expect(handlers.get('jobos:shell:open-external')?.(event, 'file:///private')).rejects.toThrow('Invalid external link')
  await handlers.get('jobos:shell:open-external')?.(event, 'https://example.com')
  expect(open).toHaveBeenCalledWith('https://example.com/')
})

test('setup, diagnostics, and connectivity retain their exact channel inventory', () => {
  const { ipc, handlers } = ipcHarness()
  const trust = vi.fn()
  const state = { runtime: null, deviceToken: null, connectivity: { state: 'disconnected', checkedAt: '', message: 'required' } } as const
  registerSetupIpc(ipc, trust, {
    configPath: '/tmp/jobos/config.json', configured: false, runtimeCredentialAvailable: false,
    runInitializer: vi.fn(async () => undefined), restart: vi.fn()
  })
  registerDiagnosticsIpc(ipc, trust, {
    configPath: '/tmp/jobos/config.json', getRuntimeState: () => state,
    getAppVersion: () => '0.1.0', isProfileSwitching: () => false,
    isRendererAvailable: () => false, openPath: vi.fn(async () => undefined)
  })
  registerConnectivityIpc(ipc, trust, {
    getState: () => state, setState: vi.fn(), ensureApiReady: vi.fn(), captureEnabled: false
  })
  expect([...handlers.keys()]).toEqual([
    'jobos:setup:get', 'jobos:setup:initialize', 'jobos:setup:restart',
    'jobos:diagnostics:get', 'jobos:diagnostics:open-data', 'jobos:diagnostics:open-logs',
    'jobos:connectivity:get'
  ])
})
