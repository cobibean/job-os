// @vitest-environment node
import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerSetupIpc } from './setupIpc.js'

function recovery(retryConnection = vi.fn().mockResolvedValue(false)) {
  const handlers = new Map<string, (event: IpcMainInvokeEvent, ...arguments_: unknown[]) => unknown>()
  const restart = vi.fn()
  const runInitializer = vi.fn().mockResolvedValue(undefined)
  registerSetupIpc({ handle: (channel, handler) => { handlers.set(channel, handler) } } as Pick<IpcMain, 'handle'>, () => undefined, {
    configPath: '/tmp/synthetic/config.json', configured: false, runtimeCredentialAvailable: true,
    runInitializer, retryConnection, restart
  })
  const invoke = (channel: string, ...arguments_: unknown[]) => handlers.get(channel)!({} as IpcMainInvokeEvent, ...arguments_)
  return { handlers, restart, runInitializer, retryConnection, invoke }
}

test('an existing installation outage offers reconnection rather than setup', () => {
  const { invoke, handlers } = recovery()
  expect(invoke('jobos:setup:get')).toMatchObject({ recovery: true })
  expect(handlers.has('jobos:setup:retry-connection')).toBe(true)
})

test('an existing installation can restart during recovery', () => {
  const { invoke, restart } = recovery()
  expect(() => invoke('jobos:setup:restart')).not.toThrow()
  expect(restart).toHaveBeenCalledOnce()
})

test('an existing installation cannot run first-run initialization during recovery', async () => {
  const { invoke, runInitializer } = recovery()
  await invoke('jobos:setup:initialize')
  expect(runInitializer).not.toHaveBeenCalled()
})

test('reconnection failure stays in recovery and a later verified connection relaunches without initialization', async () => {
  const retry = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true)
  const { invoke, restart, runInitializer } = recovery(retry)
  expect(await invoke('jobos:setup:retry-connection')).toMatchObject({ state: 'error', recovery: true })
  expect(restart).not.toHaveBeenCalled()
  expect(await invoke('jobos:setup:retry-connection')).toMatchObject({ state: 'succeeded', recovery: true })
  expect(restart).toHaveBeenCalledOnce()
  expect(runInitializer).not.toHaveBeenCalled()
})

test('concurrent connection retries probe once', async () => {
  let complete!: (connected: boolean) => void
  const retry = vi.fn(() => new Promise<boolean>(resolve => { complete = resolve }))
  const { invoke, restart } = recovery(retry)
  const pending = invoke('jobos:setup:retry-connection')
  expect(await invoke('jobos:setup:retry-connection')).toMatchObject({ state: 'working', recovery: true })
  expect(retry).toHaveBeenCalledOnce()
  complete(true)
  await pending
  expect(restart).toHaveBeenCalledOnce()
})
