import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerCareerProfileIpc } from './careerProfileIpc.js'

test('registers the complete Career Profile IPC family and resolves trust per request', () => {
  const handlers = new Map<string, (...arguments_: never[]) => unknown>()
  const ipc = { handle: (channel: string, handler: (...arguments_: never[]) => unknown) => handlers.set(channel, handler) } as Pick<IpcMain, 'handle'>
  const client = { availability: vi.fn(() => 'available') }
  const trusted = vi.fn(() => client)
  registerCareerProfileIpc(ipc, trusted as never)
  expect([...handlers.keys()]).toEqual([
    'jobos:career-profile:availability', 'jobos:career-profile:cache:validate',
    'jobos:career-profile:work-arrangement:get', 'jobos:career-profile:work-arrangement:save',
    'jobos:career-profile:work-arrangement:history', 'jobos:career-profile:work-arrangement:restore',
    'jobos:career-profile:agents:list', 'jobos:career-profile:agents:trust-mode',
    'jobos:career-profile:agents:disconnect', 'jobos:career-profile:proposals:list',
    'jobos:career-profile:proposals:decide', 'jobos:career-profile:history:get',
    'jobos:career-profile:history:undo', 'jobos:career-profile:get',
    'jobos:career-profile:items:create', 'jobos:career-profile:items:update',
    'jobos:career-profile:items:remove', 'jobos:career-profile:evidence:import',
    'jobos:career-profile:evidence:remove', 'jobos:career-profile:context:get',
    'jobos:career-profile:context:update', 'jobos:career-profile:context:preview',
    'jobos:career-profile:export', 'jobos:career-profile:restore:choose',
    'jobos:career-profile:restore'
  ])
  const event = {} as IpcMainInvokeEvent
  expect(handlers.get('jobos:career-profile:availability')?.(event)).toBe('available')
  expect(trusted).toHaveBeenCalledWith(event)
})
