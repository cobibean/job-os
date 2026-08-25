// @vitest-environment node

import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerAgentIpc } from './agentIpc.js'

test('IPC exposes only scoped operations and validates every identifier at the main seam', () => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()
  const ipc = { handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => handlers.set(channel, handler)) } as unknown as Pick<IpcMain, 'handle'>
  const client = { list: vi.fn(), create: vi.fn(), get: vi.fn(), archive: vi.fn(), send: vi.fn(), cancel: vi.fn(), review: vi.fn(), retry: vi.fn() }
  registerAgentIpc(ipc, () => client)
  const event = {} as IpcMainInvokeEvent
  expect([...handlers.keys()].sort()).toEqual([
    'jobos:agent:archive', 'jobos:agent:cancel', 'jobos:agent:create', 'jobos:agent:get',
    'jobos:agent:list', 'jobos:agent:retry', 'jobos:agent:review', 'jobos:agent:send'
  ])
  for (const channel of ['get', 'archive']) {
    expect(() => handlers.get(`jobos:agent:${channel}`)?.(event, '../conversation')).toThrow('Invalid agent conversation')
  }
  expect(() => handlers.get('jobos:agent:send')?.(event, 'conv_one', ' ', 'idempotency-01')).toThrow('Invalid agent message')
  expect(() => handlers.get('jobos:agent:cancel')?.(event, 'conv_one', '../turn')).toThrow('Invalid agent turn')
  expect(() => handlers.get('jobos:agent:review')?.(event, 'conv_one', 'turn_1', '../approval', true)).toThrow('Invalid tool review')
  expect(() => handlers.get('jobos:agent:retry')?.(event, 'x'.repeat(200), 'turn_1', 'idempotency-01')).toThrow('Invalid agent conversation')
  handlers.get('jobos:agent:send')?.(event, 'conv_one', ' hello ', 'idempotency-01')
  handlers.get('jobos:agent:retry')?.(event, 'conv_two', 'turn_2', 'idempotency-02')
  handlers.get('jobos:agent:review')?.(event, 'conv_two', 'turn_2', 'approval_abcdefghijklmnop', false)
  expect(client.send).toHaveBeenCalledWith('conv_one', 'hello', 'idempotency-01')
  expect(client.retry).toHaveBeenCalledWith('conv_two', 'turn_2', 'idempotency-02')
  expect(client.review).toHaveBeenCalledWith('conv_two', 'turn_2', 'approval_abcdefghijklmnop', false)
})
