// @vitest-environment node

import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerAgentIpc } from './agentIpc.js'

test('agent IPC exposes only fixed validated conversation operations', async () => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()
  const ipc = { handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => handlers.set(channel, handler)) } as unknown as Pick<IpcMain, 'handle'>
  const client = {
    get: vi.fn(), send: vi.fn(), cancel: vi.fn(), retry: vi.fn()
  }
  registerAgentIpc(ipc, () => client)
  const event = {} as IpcMainInvokeEvent

  expect([...handlers.keys()].sort()).toEqual([
    'jobos:agent:cancel',
    'jobos:agent:get',
    'jobos:agent:retry',
    'jobos:agent:send'
  ])
  expect(() => handlers.get('jobos:agent:send')?.(event, ' ', 'idempotency-0001')).toThrow('Invalid agent message')
  expect(() => handlers.get('jobos:agent:send')?.(event, 'ok', 'short')).toThrow('Invalid idempotency key')
  expect(() => handlers.get('jobos:agent:cancel')?.(event, '../turn')).toThrow('Invalid agent turn')
  handlers.get('jobos:agent:retry')?.(event, 'turn-1', 'idempotency-0002')
  expect(client.retry).toHaveBeenCalledWith('turn-1', 'idempotency-0002')
})
