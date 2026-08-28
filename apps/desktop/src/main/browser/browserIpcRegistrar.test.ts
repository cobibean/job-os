import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerBrowserIpc } from './browserIpc.js'

test('registers every browser operation and resolves the manager live', () => {
  const handlers = new Map<string, (...arguments_: never[]) => unknown>()
  const ipc = { handle: (channel: string, handler: (...arguments_: never[]) => unknown) => handlers.set(channel, handler) } as unknown as Pick<IpcMain, 'handle'>
  let manager: { getState: ReturnType<typeof vi.fn> } | null = null
  const assertTrusted = vi.fn()
  registerBrowserIpc(ipc, assertTrusted, () => manager as never)
  expect([...handlers.keys()]).toEqual([
    'jobos:browser:get-state', 'jobos:browser:restore', 'jobos:browser:create',
    'jobos:browser:select', 'jobos:browser:close', 'jobos:browser:reorder',
    'jobos:browser:navigate', 'jobos:browser:back', 'jobos:browser:forward',
    'jobos:browser:reload', 'jobos:browser:stop', 'jobos:browser:associate',
    'jobos:browser:copy-blocked-url', 'jobos:browser:set-bounds'
  ])
  const event = {} as IpcMainInvokeEvent
  expect(() => handlers.get('jobos:browser:get-state')?.(event)).toThrow('Browser surface unavailable')
  manager = { getState: vi.fn(() => ({ tabs: [] })) }
  expect(handlers.get('jobos:browser:get-state')?.(event)).toEqual({ tabs: [] })
  expect(assertTrusted).toHaveBeenCalledTimes(2)
})
